{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [],
      "type": "BIGINT",
      "name": "EXPR$0"
    }
  ],
  "id": "8",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 0,
          "name": "$0",
          "type": "INTEGER"
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
                    "index": 1,
                    "name": "$1",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "M",
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
                    "index": 18,
                    "name": "$18",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "SCALAR_QUERY",
                    "operator": "$SCALAR_QUERY",
                    "operands": [],
                    "query": [
                      {
                        "relOp": "LogicalSort",
                        "sort": [
                          {
                            "column": 0,
                            "type": "INTEGER"
                          }
                        ],
                        "dir": [
                          "DESCENDING"
                        ],
                        "offset": 1,
                        "limit": 1,
                        "id": "2",
                        "inputs": [
                          {
                            "relOp": "LogicalProject",
                            "project": [
                              {
                                "kind": "INPUT_REF",
                                "index": 14,
                                "name": "$14",
                                "type": "INTEGER"
                              }
                            ],
                            "id": "1",
                            "inputs": [
                              {
                                "relOp": "LogicalTableScan",
                                "table": "district",
                                "id": "0",
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
                    "index": 4,
                    "name": "$4",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "5",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "client",
                  "id": "3",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "district",
                  "id": "4",
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