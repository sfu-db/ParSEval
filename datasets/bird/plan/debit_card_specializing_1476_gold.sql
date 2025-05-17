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
  "id": "9",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "LITERAL",
          "value": 0,
          "type": "INTEGER",
          "nullable": false,
          "precision": 10
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
                "kind": "LESS_THAN",
                "operator": "<",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 1,
                    "name": "$1",
                    "type": "FLOAT"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 30000,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              },
              "variableset": "[]",
              "id": "6",
              "inputs": [
                {
                  "relOp": "LogicalAggregate",
                  "keys": [
                    {
                      "column": 0,
                      "type": "INTEGER"
                    }
                  ],
                  "aggs": [
                    {
                      "operator": "SUM",
                      "distinct": false,
                      "ignoreNulls": false,
                      "operands": [
                        {
                          "column": 1,
                          "type": "FLOAT"
                        }
                      ],
                      "type": "FLOAT",
                      "name": null
                    }
                  ],
                  "id": "5",
                  "inputs": [
                    {
                      "relOp": "LogicalProject",
                      "project": [
                        {
                          "kind": "INPUT_REF",
                          "index": 3,
                          "name": "$3",
                          "type": "INTEGER"
                        },
                        {
                          "kind": "INPUT_REF",
                          "index": 5,
                          "name": "$5",
                          "type": "FLOAT"
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
                                    "index": 1,
                                    "name": "$1",
                                    "type": "VARCHAR"
                                  },
                                  {
                                    "kind": "LITERAL",
                                    "value": "KAM",
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
                                    "kind": "OTHER_FUNCTION",
                                    "operator": "SUBSTRING",
                                    "type": "VARCHAR",
                                    "operands": [
                                      {
                                        "kind": "INPUT_REF",
                                        "index": 4,
                                        "name": "$4",
                                        "type": "VARCHAR"
                                      },
                                      {
                                        "kind": "LITERAL",
                                        "value": 1,
                                        "type": "INTEGER",
                                        "nullable": false,
                                        "precision": 10
                                      },
                                      {
                                        "kind": "LITERAL",
                                        "value": 4,
                                        "type": "INTEGER",
                                        "nullable": false,
                                        "precision": 10
                                      }
                                    ]
                                  },
                                  {
                                    "kind": "LITERAL",
                                    "value": "2012",
                                    "type": "VARCHAR",
                                    "nullable": false,
                                    "precision": -1
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
                                    "index": 0,
                                    "name": "$0",
                                    "type": "INTEGER"
                                  },
                                  {
                                    "kind": "INPUT_REF",
                                    "index": 3,
                                    "name": "$3",
                                    "type": "INTEGER"
                                  }
                                ]
                              },
                              "id": "2",
                              "inputs": [
                                {
                                  "relOp": "LogicalTableScan",
                                  "table": "customers",
                                  "id": "0",
                                  "inputs": []
                                },
                                {
                                  "relOp": "LogicalTableScan",
                                  "table": "yearmonth",
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
      ]
    }
  ]
}