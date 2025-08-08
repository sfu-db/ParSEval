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
  "id": "7",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 13,
          "name": "$13",
          "type": "INTEGER"
        }
      ],
      "id": "6",
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
                    "kind": "OTHER_FUNCTION",
                    "operator": "STRFTIME",
                    "type": "VARCHAR",
                    "operands": [
                      {
                        "kind": "LITERAL",
                        "value": "%Y",
                        "type": "CHAR",
                        "nullable": false,
                        "precision": 2
                      },
                      {
                        "kind": "CAST",
                        "operator": "CAST",
                        "type": "VARCHAR",
                        "operands": [
                          {
                            "kind": "CAST",
                            "operator": "CAST",
                            "type": "TIMESTAMP",
                            "operands": [
                              {
                                "kind": "INPUT_REF",
                                "index": 10,
                                "name": "$10",
                                "type": "DATE"
                              }
                            ]
                          }
                        ]
                      }
                    ]
                  },
                  {
                    "kind": "LITERAL",
                    "value": "2021",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              },
              {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 12,
                    "name": "$12",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "VYBER KARTOU",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              }
            ]
          },
          "variableset": "[]",
          "id": "5",
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
                    "index": 6,
                    "name": "$6",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 9,
                    "name": "$9",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "4",
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
                        "index": 1,
                        "name": "$1",
                        "type": "INTEGER"
                      },
                      {
                        "kind": "INPUT_REF",
                        "index": 4,
                        "name": "$4",
                        "type": "INTEGER"
                      }
                    ]
                  },
                  "id": "2",
                  "inputs": [
                    {
                      "relOp": "LogicalTableScan",
                      "table": "card",
                      "id": "0",
                      "inputs": []
                    },
                    {
                      "relOp": "LogicalTableScan",
                      "table": "disp",
                      "id": "1",
                      "inputs": []
                    }
                  ]
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "trans",
                  "id": "3",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}