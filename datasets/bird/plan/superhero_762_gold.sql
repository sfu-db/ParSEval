{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 2,
      "name": "$2",
      "type": "VARCHAR"
    }
  ],
  "id": "7",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 13,
                "name": "$13",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Male",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
              }
            ]
          },
          {
            "kind": "GREATER_THAN",
            "operator": ">",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "TIMES",
                "operator": "*",
                "type": "INTEGER",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 11,
                    "name": "$11",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 100,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              },
              {
                "kind": "TIMES",
                "operator": "*",
                "type": "INTEGER",
                "operands": [
                  {
                    "kind": "SCALAR_QUERY",
                    "operator": "$SCALAR_QUERY",
                    "operands": [],
                    "query": [
                      {
                        "relOp": "LogicalAggregate",
                        "keys": [],
                        "aggs": [
                          {
                            "operator": "AVG",
                            "distinct": false,
                            "ignoreNulls": false,
                            "operands": [
                              {
                                "column": 0,
                                "type": "INTEGER"
                              }
                            ],
                            "type": "INTEGER",
                            "name": "EXPR$0"
                          }
                        ],
                        "id": "2",
                        "inputs": [
                          {
                            "relOp": "LogicalProject",
                            "project": [
                              {
                                "kind": "INPUT_REF",
                                "index": 11,
                                "name": "$11",
                                "type": "INTEGER"
                              }
                            ],
                            "id": "1",
                            "inputs": [
                              {
                                "relOp": "LogicalTableScan",
                                "table": "superhero",
                                "id": "0",
                                "inputs": []
                              }
                            ]
                          }
                        ]
                      }
                    ]
                  },
                  {
                    "kind": "LITERAL",
                    "value": 79,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              }
            ]
          }
        ]
      },
      "variableset": "[]",
      "id": "6",
      "inputs": [
        {
          "relOp": "LogicalJoin",
          "joinType": "inner",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 3,
                "name": "$3",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 12,
                "name": "$12",
                "type": "INTEGER"
              }
            ]
          },
          "id": "5",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "superhero",
              "id": "3",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "gender",
              "id": "4",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}