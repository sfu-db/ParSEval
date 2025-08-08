{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "BIGINT"
    }
  ],
  "dir": [
    "ASCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "7",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 2,
          "name": "$2",
          "type": "BIGINT"
        }
      ],
      "id": "6",
      "inputs": [
        {
          "relOp": "LogicalAggregate",
          "keys": [
            {
              "column": 0,
              "type": "INTEGER"
            },
            {
              "column": 1,
              "type": "VARCHAR"
            }
          ],
          "aggs": [
            {
              "operator": "COUNT",
              "distinct": false,
              "ignoreNulls": false,
              "operands": [],
              "type": "BIGINT",
              "name": "ASC"
            }
          ],
          "id": "5",
          "inputs": [
            {
              "relOp": "LogicalProject",
              "project": [
                {
                  "kind": "INPUT_REF",
                  "index": 7,
                  "name": "$7",
                  "type": "INTEGER"
                },
                {
                  "kind": "INPUT_REF",
                  "index": 118,
                  "name": "$118",
                  "type": "VARCHAR"
                }
              ],
              "id": "4",
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
                            "index": 3,
                            "name": "$3",
                            "type": "VARCHAR"
                          },
                          {
                            "kind": "LITERAL",
                            "value": "2015/2016",
                            "type": "VARCHAR",
                            "nullable": false,
                            "precision": -1
                          }
                        ]
                      },
                      {
                        "kind": "LESS_THAN",
                        "operator": "<",
                        "type": "BOOLEAN",
                        "operands": [
                          {
                            "kind": "MINUS",
                            "operator": "-",
                            "type": "INTEGER",
                            "operands": [
                              {
                                "kind": "INPUT_REF",
                                "index": 9,
                                "name": "$9",
                                "type": "INTEGER"
                              },
                              {
                                "kind": "INPUT_REF",
                                "index": 10,
                                "name": "$10",
                                "type": "INTEGER"
                              }
                            ]
                          },
                          {
                            "kind": "LITERAL",
                            "value": 0,
                            "type": "INTEGER",
                            "nullable": false,
                            "precision": 10
                          }
                        ]
                      }
                    ]
                  },
                  "variableset": "[]",
                  "id": "3",
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
                            "index": 7,
                            "name": "$7",
                            "type": "INTEGER"
                          },
                          {
                            "kind": "INPUT_REF",
                            "index": 116,
                            "name": "$116",
                            "type": "INTEGER"
                          }
                        ]
                      },
                      "id": "2",
                      "inputs": [
                        {
                          "relOp": "LogicalTableScan",
                          "table": "Match",
                          "id": "0",
                          "inputs": []
                        },
                        {
                          "relOp": "LogicalTableScan",
                          "table": "Team",
                          "id": "1",
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
    }
  ]
}